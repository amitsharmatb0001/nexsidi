"""Context Window Manager — Auto-manage context for long-running agents.

When an agent's context approaches 80% of the model's limit, this service:
1. Summarizes the current context via AI
2. Archives the full context to context_engine
3. Returns a fresh context with summary + critical state

Prevents: Context overflow crashes in long agentic loops (Shubham, Aanya).

Integration:
- call_ai_with_tools() checks context size before each iteration
- Long-running agents call check_and_manage() proactively
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Token limits per model key (from ai_router MODELS registry)
MODEL_TOKEN_LIMITS: dict[str, int] = {
    # Claude models
    "haiku": 200_000,
    "sonnet": 200_000,
    "sonnet-4.5": 200_000,
    "opus": 200_000,
    # Gemini models
    "gemini-flash": 128_000,
    "gemini-pro": 128_000,
    "gemini-3-flash": 128_000,
    "gemini-3.1-flash-lite": 128_000,
    "gemini-3.1-pro": 128_000,
}

# Threshold: summarize when context exceeds this fraction of model limit
_THRESHOLD = 0.80

# Rough token estimation: 4 characters ≈ 1 token (tiktoken fallback)
_CHARS_PER_TOKEN = 4


class ContextWindowManager:
    """Auto-manage context window for long-running agents.

    Usage:
        from app.services.context_window_manager import context_window_manager

        status = context_window_manager.check_context_size(context_str, model="sonnet")
        if status["needs_summary"]:
            fresh_context = await context_window_manager.summarize_and_archive(
                agent_name="shubham",
                pipeline_run_id="run-123",
                context=context_str,
                critical_state={"files_written": [...], "current_step": "..."},
            )
    """

    def count_tokens(self, text: str, model: str | None = None) -> int:
        """Estimate token count for the given model.

        TOKENIZER-FIX: Uses model-appropriate counting instead of hardcoding
        GPT-4's cl100k_base for all models. Claude and Gemini use different
        tokenizers — cl100k_base gives ~15-20% wrong counts for them, causing
        premature context summarization or context overflow.

        - OpenAI models: Use tiktoken with correct model encoding
        - Claude models: ~3.5 chars per token (measured average)
        - Gemini models: ~3.8 chars per token (SentencePiece-based)
        - Unknown/fallback: 4 chars per token
        """
        model = model or ""
        model_lower = model.lower()

        # Claude models: different tokenizer, use char-based estimate
        if any(name in model_lower for name in ("claude", "haiku", "sonnet", "opus")):
            return int(len(text) / 3.5)

        # Gemini models: SentencePiece-based, slightly different ratio
        if any(name in model_lower for name in ("gemini",)):
            return int(len(text) / 3.8)

        # OpenAI models: use tiktoken with correct model
        try:
            import tiktoken
            try:
                enc = tiktoken.encoding_for_model(model or "gpt-4")
            except KeyError:
                enc = tiktoken.encoding_for_model("gpt-4")  # fallback
            return len(enc.encode(text))
        except (ImportError, Exception):
            return len(text) // _CHARS_PER_TOKEN

    def check_context_size(
        self,
        context: str,
        model: str = "sonnet",
    ) -> dict[str, Any]:
        """Check if context needs summarization.

        Returns:
            {"needs_summary": bool, "token_count": int, "limit": int, "usage_pct": float}
        """
        token_count = self.count_tokens(context)
        limit = MODEL_TOKEN_LIMITS.get(model, 128_000)
        usage_pct = token_count / limit if limit > 0 else 0.0

        return {
            "needs_summary": usage_pct >= _THRESHOLD,
            "token_count": token_count,
            "limit": limit,
            "usage_pct": round(usage_pct, 3),
        }

    async def summarize_and_archive(
        self,
        agent_name: str,
        pipeline_run_id: str,
        context: str,
        critical_state: dict[str, Any],
        model: str = "sonnet",
    ) -> str:
        """Summarize context via AI, archive full version, return fresh context.

        Args:
            agent_name: Which agent's context this is
            pipeline_run_id: Pipeline run ID for context_engine storage
            context: The full context string to summarize
            critical_state: Key state that MUST be preserved (not summarized)
            model: Model key for routing the summarization call

        Returns:
            Fresh context string with summary + critical state
        """
        # 1. Archive full context
        await self._archive_context(agent_name, pipeline_run_id, context)

        # 2. Summarize via AI
        summary = await self._summarize_via_ai(agent_name, context, model)

        # 3. Build fresh context: summary + critical state
        import json
        critical_json = json.dumps(critical_state, indent=2, default=str)

        fresh_context = (
            f"=== CONTEXT SUMMARY (previous work archived) ===\n"
            f"{summary}\n\n"
            f"=== CRITICAL STATE (must preserve) ===\n"
            f"{critical_json}\n\n"
            f"=== CONTINUE FROM HERE ===\n"
        )

        logger.info(
            "context_summarized",
            agent=agent_name,
            pipeline_run_id=pipeline_run_id,
            original_tokens=self.count_tokens(context),
            summary_tokens=self.count_tokens(fresh_context),
        )

        return fresh_context

    async def _archive_context(
        self,
        agent_name: str,
        pipeline_run_id: str,
        context: str,
    ) -> None:
        """Archive full context to context_engine for debug/support."""
        try:
            from app.services.context_engine import get_context_engine
            engine = get_context_engine()
            await engine.store(
                pipeline_run_id,
                f"__archived_context_{agent_name}__",
                {"full_context": context[:500_000]},  # Cap at 500K chars
            )
        except Exception as exc:
            logger.warning("context_archive_failed", error=str(exc)[:200])

    async def _summarize_via_ai(
        self,
        agent_name: str,
        context: str,
        model: str,
    ) -> str:
        """Summarize context using AI."""
        try:
            from app.services.ai_router import AIMessage, AIRequest, TaskComplexity, get_ai_router

            router = get_ai_router()
            request = AIRequest(
                messages=[AIMessage(
                    role="user",
                    content=(
                        f"Summarize this agent's work context concisely. "
                        f"Preserve: file names, function signatures, key decisions, "
                        f"error patterns encountered, and current progress.\n\n"
                        f"CONTEXT:\n{context[:50_000]}"  # Cap input to summarizer
                    ),
                )],
                system_prompt=(
                    f"You are summarizing the work context of {agent_name}, "
                    f"a code generation agent. Create a dense but complete summary "
                    f"that another AI can use to continue the work seamlessly."
                ),
                task_type="summarization",
                complexity=TaskComplexity.SIMPLE,
                temperature=0.2,
                max_tokens=4000,
            )
            response = await router.call(request)
            return response.content

        except Exception as exc:
            logger.warning("context_summarize_ai_failed", error=str(exc)[:200])
            # Fallback: take first + last 2000 chars
            if len(context) > 4000:
                return (
                    f"[AI summarization failed — using head+tail truncation]\n"
                    f"{context[:2000]}\n...\n{context[-2000:]}"
                )
            return context


# Module-level singleton
context_window_manager = ContextWindowManager()
