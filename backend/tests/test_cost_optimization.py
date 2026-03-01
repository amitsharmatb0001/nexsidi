"""Tests for cost optimization features.

Covers:
- SharedContext prompt caching (Anthropic system blocks)
- GeminiCacheManager (context caching for large prompts)
- Anthropic Batch API (call_batch / get_batch_results)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai_router import (
    AIMessage,
    AIRequest,
    AIResponse,
    AIRouter,
    GeminiCacheManager,
    ModelSpec,
    Provider,
    SharedContext,
    TaskComplexity,
    get_gemini_cache_manager,
)


# ── TestSharedContext ───────────────────────────────────────────────


class TestSharedContext:
    """Test SharedContext prompt caching support."""

    def test_empty_context_produces_no_blocks(self):
        ctx = SharedContext()
        assert ctx.to_anthropic_system_blocks() == []

    def test_empty_strings_produce_no_blocks(self):
        ctx = SharedContext(project_spec="", architecture_contract="")
        assert ctx.to_anthropic_system_blocks() == []

    def test_spec_only_produces_one_block(self):
        ctx = SharedContext(project_spec="Build a todo app")
        blocks = ctx.to_anthropic_system_blocks()
        assert len(blocks) == 1
        assert "Project Specification" in blocks[0]["text"]
        assert "todo app" in blocks[0]["text"]
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}

    def test_contract_only_produces_one_block(self):
        ctx = SharedContext(architecture_contract='{"tables": ["users"]}')
        blocks = ctx.to_anthropic_system_blocks()
        assert len(blocks) == 1
        assert "Architecture Contract" in blocks[0]["text"]

    def test_both_fields_merged_into_one_block(self):
        ctx = SharedContext(
            project_spec="Build a todo app",
            architecture_contract='{"tables": ["users"]}',
        )
        blocks = ctx.to_anthropic_system_blocks()
        assert len(blocks) == 1
        text = blocks[0]["text"]
        assert "Project Specification" in text
        assert "Architecture Contract" in text

    def test_cache_control_is_ephemeral(self):
        ctx = SharedContext(project_spec="x")
        blocks = ctx.to_anthropic_system_blocks()
        assert blocks[0]["cache_control"]["type"] == "ephemeral"

    def test_to_gemini_context_text_empty(self):
        ctx = SharedContext()
        assert ctx.to_gemini_context_text() == ""

    def test_to_gemini_context_text_combines(self):
        ctx = SharedContext(
            project_spec="Todo app",
            architecture_contract="Contract",
        )
        text = ctx.to_gemini_context_text()
        assert "Todo app" in text
        assert "Contract" in text

    def test_frozen_dataclass(self):
        ctx = SharedContext(project_spec="x")
        with pytest.raises(AttributeError):
            ctx.project_spec = "y"  # type: ignore[misc]


# ── TestAnthropicBodyWithSharedContext ──────────────────────────────


class TestAnthropicBodyWithSharedContext:
    """Test that _build_anthropic_body integrates SharedContext."""

    def _make_router(self) -> AIRouter:
        router = AIRouter.__new__(AIRouter)
        router._anthropic_key = "test-key"
        return router

    def _make_spec(self) -> ModelSpec:
        return ModelSpec(
            model_id="claude-sonnet-4-6-20250514",
            provider=Provider.ANTHROPIC,
            display_name="Test Sonnet",
            cost_tier=3,
            max_output_tokens=64000,
        )

    def test_body_without_shared_context_has_one_system_block(self):
        router = self._make_router()
        req = AIRequest(
            messages=[AIMessage(role="user", content="hi")],
            system_prompt="You are a helper",
            cache_system_prompt=True,
        )
        body = router._build_anthropic_body(self._make_spec(), req)
        assert isinstance(body["system"], list)
        assert len(body["system"]) == 1
        assert body["system"][0]["text"] == "You are a helper"

    def test_body_with_shared_context_has_two_system_blocks(self):
        router = self._make_router()
        ctx = SharedContext(project_spec="Todo app spec")
        req = AIRequest(
            messages=[AIMessage(role="user", content="hi")],
            system_prompt="You are a helper",
            cache_system_prompt=True,
            shared_context=ctx,
        )
        body = router._build_anthropic_body(self._make_spec(), req)
        assert isinstance(body["system"], list)
        assert len(body["system"]) == 2
        # First block is shared context
        assert "Project Specification" in body["system"][0]["text"]
        assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
        # Second block is agent-specific
        assert body["system"][1]["text"] == "You are a helper"

    def test_body_with_empty_shared_context_has_one_block(self):
        router = self._make_router()
        ctx = SharedContext()  # empty
        req = AIRequest(
            messages=[AIMessage(role="user", content="hi")],
            system_prompt="You are a helper",
            cache_system_prompt=True,
            shared_context=ctx,
        )
        body = router._build_anthropic_body(self._make_spec(), req)
        assert len(body["system"]) == 1

    def test_body_no_cache_uses_plain_string(self):
        router = self._make_router()
        req = AIRequest(
            messages=[AIMessage(role="user", content="hi")],
            system_prompt="You are a helper",
            cache_system_prompt=False,
        )
        body = router._build_anthropic_body(self._make_spec(), req)
        assert body["system"] == "You are a helper"


# ── TestGoogleBodyWithCachedContent ─────────────────────────────────


class TestGoogleBodyWithCachedContent:
    """Test that _build_google_body handles cached_content."""

    def _make_router(self) -> AIRouter:
        router = AIRouter.__new__(AIRouter)
        return router

    def _make_spec(self) -> ModelSpec:
        return ModelSpec(
            model_id="gemini-2.5-flash",
            provider=Provider.GOOGLE,
            display_name="Gemini Flash",
            cost_tier=1,
            max_output_tokens=65536,
        )

    def test_body_without_cache_has_system_instruction(self):
        router = self._make_router()
        req = AIRequest(
            messages=[AIMessage(role="user", content="hi")],
            system_prompt="You are a helper",
        )
        body = router._build_google_body(self._make_spec(), req)
        assert "systemInstruction" in body
        assert "cachedContent" not in body

    def test_body_with_cache_injects_system_as_preamble(self):
        router = self._make_router()
        req = AIRequest(
            messages=[AIMessage(role="user", content="hi")],
            system_prompt="You are a helper",
        )
        body = router._build_google_body(
            self._make_spec(), req, cached_content="cachedContents/abc123"
        )
        assert "cachedContent" in body
        assert body["cachedContent"] == "cachedContents/abc123"
        # C1-REFIX: Google API returns 400 if both cachedContent AND
        # systemInstruction are set.  System prompt is injected as a
        # leading user message instead.
        assert "systemInstruction" not in body
        # System prompt should be first content entry as preamble
        assert body["contents"][0]["role"] == "user"
        assert "[System Instructions]" in body["contents"][0]["parts"][0]["text"]
        assert "You are a helper" in body["contents"][0]["parts"][0]["text"]

    def test_body_no_system_prompt_no_instruction(self):
        router = self._make_router()
        req = AIRequest(
            messages=[AIMessage(role="user", content="hi")],
        )
        body = router._build_google_body(self._make_spec(), req)
        assert "systemInstruction" not in body
        assert "cachedContent" not in body


# ── TestGeminiCacheManager ──────────────────────────────────────────


class TestGeminiCacheManager:
    """Test Gemini context caching manager."""

    @pytest.mark.asyncio
    async def test_small_content_returns_none(self):
        mgr = GeminiCacheManager()
        result = await mgr.get_or_create_cache(
            AsyncMock(), "gemini-2.5-flash", "short text", "key123"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_small_content_skips_api_call(self):
        mgr = GeminiCacheManager()
        http = AsyncMock()
        result = await mgr.get_or_create_cache(
            http, "gemini-2.5-flash", "short", "key123"
        )
        assert result is None
        http.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_large_content_calls_api(self):
        mgr = GeminiCacheManager()
        http = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"name": "cachedContents/test123"}
        http.post.return_value = mock_resp

        large_text = "x" * 200_000  # >128K chars
        result = await mgr.get_or_create_cache(
            http, "gemini-2.5-flash", large_text, "key123"
        )
        assert result == "cachedContents/test123"
        http.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_hit_reuses_name(self):
        mgr = GeminiCacheManager()
        http = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"name": "cachedContents/abc"}
        http.post.return_value = mock_resp

        large_text = "y" * 200_000
        # First call — creates cache
        result1 = await mgr.get_or_create_cache(
            http, "gemini-2.5-flash", large_text, "key123"
        )
        # Second call — should hit cache
        result2 = await mgr.get_or_create_cache(
            http, "gemini-2.5-flash", large_text, "key123"
        )
        assert result1 == result2 == "cachedContents/abc"
        assert http.post.call_count == 1  # Only one API call

    @pytest.mark.asyncio
    async def test_api_failure_returns_none(self):
        mgr = GeminiCacheManager()
        http = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        http.post.return_value = mock_resp

        large_text = "z" * 200_000
        result = await mgr.get_or_create_cache(
            http, "gemini-2.5-flash", large_text, "key123"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_api_exception_returns_none(self):
        mgr = GeminiCacheManager()
        http = AsyncMock()
        http.post.side_effect = Exception("Network error")

        large_text = "w" * 200_000
        result = await mgr.get_or_create_cache(
            http, "gemini-2.5-flash", large_text, "key123"
        )
        assert result is None

    def test_clear_empties_cache(self):
        mgr = GeminiCacheManager()
        mgr._cache["hash1"] = "cachedContents/x"
        assert len(mgr._cache) == 1
        mgr.clear()
        assert len(mgr._cache) == 0

    def test_get_gemini_cache_manager_singleton(self):
        mgr1 = get_gemini_cache_manager()
        mgr2 = get_gemini_cache_manager()
        assert mgr1 is mgr2

    def test_min_chars_threshold(self):
        assert GeminiCacheManager._MIN_CHARS_FOR_CACHE == 128_000


# ── TestBatchAPI ────────────────────────────────────────────────────


class TestBatchAPI:
    """Test Anthropic Batch API methods."""

    def _make_router(self) -> AIRouter:
        import asyncio
        router = AIRouter.__new__(AIRouter)
        router._anthropic_key = "test-key"
        router._google_key = ""
        router._use_vertex = False
        router._circuits = {
            Provider.ANTHROPIC: MagicMock(is_available=AsyncMock(return_value=True), record_success=AsyncMock(), record_failure=AsyncMock()),
            Provider.GOOGLE: MagicMock(is_available=AsyncMock(return_value=True), record_success=AsyncMock(), record_failure=AsyncMock()),
        }
        router._http = None
        router._http_lock = asyncio.Lock()
        return router

    @pytest.mark.asyncio
    async def test_call_batch_builds_payload(self):
        router = self._make_router()
        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"id": "batch_123"}
        mock_http.post.return_value = mock_resp
        router._http = mock_http

        requests = [
            AIRequest(
                messages=[AIMessage(role="user", content="hello")],
                complexity=TaskComplexity.HIGH,
            ),
        ]

        with patch("app.services.ai_router.get_ai_mode", return_value="mixed"):
            batch_id = await router.call_batch(requests)

        assert batch_id == "batch_123"
        mock_http.post.assert_called_once()
        call_args = mock_http.post.call_args
        assert "messages/batches" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_call_batch_skips_google_requests(self):
        router = self._make_router()
        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"id": "batch_456"}
        mock_http.post.return_value = mock_resp
        router._http = mock_http

        # LOW complexity in mixed mode routes to gemini-flash (Google)
        requests = [
            AIRequest(
                messages=[AIMessage(role="user", content="hello")],
                complexity=TaskComplexity.LOW,
            ),
        ]

        with patch("app.services.ai_router.get_ai_mode", return_value="mixed"):
            with pytest.raises(ValueError, match="No Anthropic"):
                await router.call_batch(requests)

    @pytest.mark.asyncio
    async def test_get_batch_results_returns_none_when_processing(self):
        router = self._make_router()
        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"processing_status": "in_progress"}
        mock_http.get.return_value = mock_resp
        router._http = mock_http

        result = await router.get_batch_results("msgbatch_789")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_batch_results_parses_completed(self):
        import orjson

        router = self._make_router()

        # Status check response
        status_resp = MagicMock()
        status_resp.raise_for_status = MagicMock()
        status_resp.json.return_value = {
            "processing_status": "ended",
            "results_url": "https://api.anthropic.com/results/abc",
        }

        # Results JSONL response
        results_line = orjson.dumps({
            "custom_id": "req_0",
            "result": {
                "type": "succeeded",
                "message": {
                    "model": "claude-sonnet-4-6-20250514",
                    "content": [{"type": "text", "text": "Hello world"}],
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            },
        }).decode()

        results_resp = MagicMock()
        results_resp.raise_for_status = MagicMock()
        results_resp.text = results_line

        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_http.get.side_effect = [status_resp, results_resp]
        router._http = mock_http

        responses = await router.get_batch_results("msgbatch_done")
        assert responses is not None
        assert len(responses) == 1
        assert responses[0].content == "Hello world"
        assert responses[0].provider == Provider.ANTHROPIC
        assert responses[0].input_tokens == 100
        assert responses[0].output_tokens == 50

    @pytest.mark.asyncio
    async def test_empty_batch_raises(self):
        router = self._make_router()
        router._http = AsyncMock()

        with patch("app.services.ai_router.get_ai_mode", return_value="mixed"):
            with pytest.raises(ValueError, match="No Anthropic"):
                await router.call_batch([])


# ── TestAIRequestSharedContext ──────────────────────────────────────


class TestAIRequestSharedContext:
    """Test that AIRequest accepts shared_context."""

    def test_default_is_none(self):
        req = AIRequest(messages=[AIMessage(role="user", content="hi")])
        assert req.shared_context is None

    def test_can_set_shared_context(self):
        ctx = SharedContext(project_spec="Todo app")
        req = AIRequest(
            messages=[AIMessage(role="user", content="hi")],
            shared_context=ctx,
        )
        assert req.shared_context is ctx
        assert req.shared_context.project_spec == "Todo app"
