"""
API Provider Resilience with Circuit Breaker Pattern
=====================================================

Automatic failover between AI providers with circuit breaker pattern.

Provider Chain:
1. Anthropic Claude (Primary)
2. Google Gemini (Fallback 1)
3. Groq (Fallback 2)

Features:
- Circuit breaker per provider
- Automatic failover on failure
- Health checks
- Metrics tracking
- Exponential backoff
"""

import logging
import time
import asyncio
from typing import Optional, Dict, Any, List, Callable
from enum import Enum
from datetime import datetime, timedelta
from dataclasses import dataclass, field


class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Too many failures, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreaker:
    """Circuit breaker for a single provider"""
    provider_name: str
    failure_threshold: int = 5  # Open circuit after N failures
    timeout_seconds: int = 60  # How long to keep circuit open
    half_open_max_calls: int = 3  # Max calls to test in half-open state
    
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: Optional[datetime] = None
    success_count: int = 0
    total_calls: int = 0
    
    def record_success(self):
        """Record successful call"""
        self.total_calls += 1
        self.success_count += 1
        self.failure_count = 0
        
        # If in half-open, close circuit after successful calls
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
    
    def record_failure(self):
        """Record failed call"""
        self.total_calls += 1
        self.failure_count += 1
        self.last_failure_time = datetime.now()
        
        # Open circuit if threshold exceeded
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
    
    def can_attempt(self) -> bool:
        """Check if we can attempt a call"""
        if self.state == CircuitState.CLOSED:
            return True
        
        if self.state == CircuitState.OPEN:
            # Check if timeout has passed
            if self.last_failure_time:
                elapsed = (datetime.now() - self.last_failure_time).total_seconds()
                if elapsed >= self.timeout_seconds:
                    # Move to half-open to test
                    self.state = CircuitState.HALF_OPEN
                    self.failure_count = 0
                    return True
            return False
        
        if self.state == CircuitState.HALF_OPEN:
            # Allow limited calls to test
            return self.total_calls < self.half_open_max_calls
        
        return False
    
    def get_success_rate(self) -> float:
        """Calculate success rate"""
        if self.total_calls == 0:
            return 0.0
        return self.success_count / self.total_calls


class APIResilience:
    """
    API provider resilience with circuit breaker and automatic failover.
    """
    
    def __init__(self):
        self.logger = logging.getLogger("api_resilience")
        
        # Circuit breakers for each provider
        self.circuits: Dict[str, CircuitBreaker] = {
            "anthropic": CircuitBreaker("anthropic"),
            "google": CircuitBreaker("google"),
            "groq": CircuitBreaker("groq")
        }
        
        # Provider priority (fallback chain)
        self.provider_chain = ["anthropic", "google", "groq"]
        
        # Metrics
        self.metrics: Dict[str, Dict[str, Any]] = {
            provider: {
                "total_calls": 0,
                "successful_calls": 0,
                "failed_calls": 0,
                "avg_latency_ms": 0.0,
                "last_used": None
            }
            for provider in self.provider_chain
        }
    
    async def call_with_fallback(
        self,
        api_call: Callable,
        provider: str = "anthropic",
        *args,
        **kwargs
    ) -> Any:
        """
        Call API with automatic failover to next provider on failure.
        
        Args:
            api_call: Async function to call (should accept provider name)
            provider: Preferred provider (default: anthropic)
            *args, **kwargs: Arguments to pass to api_call
        
        Returns:
            API response
        
        Raises:
            Exception if all providers fail
        """
        # Start with preferred provider, then try fallback chain
        providers_to_try = [provider] + [p for p in self.provider_chain if p != provider]
        
        last_exception = None
        
        for current_provider in providers_to_try:
            circuit = self.circuits[current_provider]
            
            # Check circuit breaker
            if not circuit.can_attempt():
                self.logger.warning(
                    f"[WARN] Circuit breaker OPEN for {current_provider}, "
                    f"state={circuit.state.value}, failures={circuit.failure_count}"
                )
                continue
            
            try:
                self.logger.info(f"🔄 Attempting API call with {current_provider}")
                
                start_time = time.time()
                
                # Make the API call
                result = await api_call(current_provider, *args, **kwargs)
                
                # Record success
                latency_ms = (time.time() - start_time) * 1000
                self._record_success(current_provider, latency_ms)
                circuit.record_success()
                
                self.logger.info(
                    f"[OK] {current_provider} succeeded "
                    f"(latency={latency_ms:.0f}ms, success_rate={circuit.get_success_rate():.2%})"
                )
                
                return result
                
            except Exception as e:
                # Record failure
                self._record_failure(current_provider)
                circuit.record_failure()
                
                self.logger.error(
                    f"[ERROR] {current_provider} failed: {type(e).__name__}: {e} "
                    f"(failures={circuit.failure_count}/{circuit.failure_threshold})"
                )
                
                last_exception = e
                
                # Try next provider
                continue
        
        # All providers failed
        self.logger.error("[ERROR] All API providers failed!")
        raise Exception(f"All API providers failed. Last error: {last_exception}")
    
    def _record_success(self, provider: str, latency_ms: float):
        """Record successful API call"""
        metrics = self.metrics[provider]
        metrics["total_calls"] += 1
        metrics["successful_calls"] += 1
        metrics["last_used"] = datetime.now().isoformat()
        
        # Update average latency (exponential moving average)
        alpha = 0.3
        metrics["avg_latency_ms"] = (
            alpha * latency_ms + (1 - alpha) * metrics["avg_latency_ms"]
        )
    
    def _record_failure(self, provider: str):
        """Record failed API call"""
        metrics = self.metrics[provider]
        metrics["total_calls"] += 1
        metrics["failed_calls"] += 1
    
    async def check_provider_health(self, provider: str) -> Dict[str, Any]:
        """
        Check health of a specific provider.
        
        Args:
            provider: Provider name
        
        Returns:
            Dict with health status
        """
        circuit = self.circuits[provider]
        metrics = self.metrics[provider]
        
        return {
            "provider": provider,
            "circuit_state": circuit.state.value,
            "success_rate": circuit.get_success_rate(),
            "total_calls": metrics["total_calls"],
            "successful_calls": metrics["successful_calls"],
            "failed_calls": metrics["failed_calls"],
            "avg_latency_ms": metrics["avg_latency_ms"],
            "last_used": metrics["last_used"]
        }
    
    def get_all_health_status(self) -> List[Dict[str, Any]]:
        """Get health status for all providers"""
        return [
            {
                "provider": provider,
                "circuit_state": self.circuits[provider].state.value,
                "success_rate": self.circuits[provider].get_success_rate(),
                **self.metrics[provider]
            }
            for provider in self.provider_chain
        ]
    
    def reset_circuit(self, provider: str):
        """Manually reset circuit breaker for a provider"""
        circuit = self.circuits[provider]
        circuit.state = CircuitState.CLOSED
        circuit.failure_count = 0
        self.logger.info(f"🔄 Reset circuit breaker for {provider}")


# Global instance
_api_resilience: Optional[APIResilience] = None


def get_api_resilience() -> APIResilience:
    """Get or create global API resilience instance"""
    global _api_resilience
    
    if _api_resilience is None:
        _api_resilience = APIResilience()
    
    return _api_resilience
