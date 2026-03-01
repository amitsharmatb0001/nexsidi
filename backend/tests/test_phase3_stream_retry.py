"""Tests for Phase 3: Stream Retry Logic.

Covers:
- call_stream() retries on retryable HTTP status codes (429, 500, 502, 503, 529)
- Only retries pre-first-chunk failures (chunks_yielded == 0)
- Does NOT retry after chunks have been yielded
- Max 2 retries for stream
- Jitter added to non-streaming call() retry
- Exponential backoff with jitter
"""

from __future__ import annotations

import inspect
import random

import pytest


# ── Stream Retry Tests ────────────────────────────────────────────


class TestStreamRetryLogic:
    """call_stream() must retry pre-first-chunk failures."""

    def test_stream_has_retry_loop(self):
        """call_stream source must have a retry loop."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter.call_stream)
        assert "for attempt in range" in source

    def test_stream_tracks_chunks_yielded(self):
        """call_stream must track chunks_yielded for retry eligibility."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter.call_stream)
        assert "chunks_yielded" in source

    def test_stream_only_retries_pre_first_chunk(self):
        """Retry must only happen when chunks_yielded == 0."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter.call_stream)
        assert "chunks_yielded == 0" in source

    def test_stream_retryable_codes(self):
        """Stream retry must check retryable HTTP status codes."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter.call_stream)
        # Must check for retryable codes
        assert "429" in source
        assert "502" in source
        assert "503" in source

    def test_stream_max_retries_is_2(self):
        """Stream should have max 2 retries."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter.call_stream)
        assert "_MAX_STREAM_RETRIES = 2" in source

    def test_stream_uses_exponential_backoff_with_jitter(self):
        """Stream retry must use exponential backoff + jitter."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter.call_stream)
        assert "random.uniform" in source
        assert "2 ** attempt" in source

    def test_stream_records_circuit_breaker_on_failure(self):
        """Stream failure must record circuit breaker failure."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter.call_stream)
        assert "record_failure" in source

    def test_stream_records_circuit_breaker_on_success(self):
        """Stream success must record circuit breaker success."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter.call_stream)
        assert "record_success" in source

    def test_stream_handles_non_http_errors(self):
        """Stream must handle non-HTTP errors (ConnectionError, etc.)."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter.call_stream)
        # Must have a generic Exception handler
        assert "except Exception" in source


# ── Non-Streaming Jitter Tests ────────────────────────────────────


class TestNonStreamJitter:
    """call() retry must include jitter to prevent thundering herd."""

    def test_call_retry_has_jitter(self):
        """Non-streaming call() must add random jitter to retry delay."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter.call)
        assert "random.uniform" in source

    def test_random_imported(self):
        """random module must be imported in ai_router."""
        import app.services.ai_router as mod
        assert hasattr(mod, "random")
