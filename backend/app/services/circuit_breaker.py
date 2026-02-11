"""
Circuit Breaker - Prevent Cascading Failures
"""

import time
import logging
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger("circuit_breaker")


class CircuitState(Enum):
    CLOSED = "closed"        # Normal
    OPEN = "open"            # Failing
    HALF_OPEN = "half_open"  # Testing


class CircuitBreaker:
    """Circuit breaker for external services"""
    
    def __init__(self, failure_threshold=5, timeout=60):
        """
        Args:
            failure_threshold: Failures before opening
            timeout: Seconds before testing recovery
        """
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = CircuitState.CLOSED
    
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute with circuit breaker protection"""
        
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.timeout:
                logger.info("Circuit breaker: OPEN → HALF_OPEN")
                self.state = CircuitState.HALF_OPEN
            else:
                raise Exception(f"Circuit breaker OPEN - service unavailable")
        
        try:
            result = await func(*args, **kwargs)
            
            if self.state == CircuitState.HALF_OPEN:
                logger.info("Circuit breaker: HALF_OPEN → CLOSED")
                self.state = CircuitState.CLOSED
                self.failure_count = 0
            
            return result
            
        except Exception as e:
            self.failure_count += 1
            self.last_failure_time = time.time()
            
            if self.failure_count >= self.failure_threshold:
                if self.state != CircuitState.OPEN:
                    logger.error(f"[WARN] Circuit breaker: OPEN after {self.failure_count} failures")
                    self.state = CircuitState.OPEN
            
            raise


# Global breakers
gcp_breaker = CircuitBreaker(failure_threshold=3, timeout=300)
external_api_breaker = CircuitBreaker(failure_threshold=5, timeout=60)
