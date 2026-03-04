"""Research Agent — Web search via Gemini with Google Search Grounding.

Enables any agent to search the web for:
- Error solutions (when encountering unknown errors)
- Library documentation (when using unfamiliar frameworks)
- Best practices (when making architectural decisions)

Uses Gemini's built-in Google Search Grounding feature: real web search
at zero extra cost (included in Gemini API). Falls back to AI knowledge
if Gemini unavailable.

Integration:
- Fixer: searches for error solutions before attempting fixes
- Any agent: can instantiate ResearchAgent and call search methods
- Not a pipeline agent — utility agent called on-demand
"""

from __future__ import annotations

from typing import Any

import structlog

from app.services.ai_router import TaskComplexity

logger = structlog.get_logger(__name__)


class ResearchAgent:
    """Web search via Gemini with Google Search Grounding.

    Usage:
        researcher = ResearchAgent("pipeline-run-123")
        result = await researcher.search_for_solution(
            error_msg="ModuleNotFoundError: No module named 'app.core.security'",
            context="FastAPI project with JWT authentication",
        )
        # result = {"findings": [...], "summary": "...", "sources": [...]}
    """

    name = "research"
    display_name = "Research Agent"
    default_complexity = TaskComplexity.SIMPLE
    default_model = "gemini-flash"  # Use Gemini for Google Search Grounding

    def __init__(self, pipeline_run_id: str) -> None:
        self.pipeline_run_id = pipeline_run_id

    async def search_for_solution(
        self,
        error_msg: str,
        context: str = "",
    ) -> dict[str, Any]:
        """Search web for error solution using Gemini + Google Search.

        Args:
            error_msg: The error message to find a fix for
            context: Additional context (framework, file, etc.)

        Returns:
            {"findings": [...], "summary": str, "sources": [...]}
        """
        query = (
            f"How to fix this error:\n{error_msg[:500]}\n\n"
            f"Context: {context[:300]}\n\n"
            f"Provide a specific, actionable fix with code example."
        )

        return await self._search(query, "error_solution")

    async def search_documentation(
        self,
        library: str,
        topic: str,
    ) -> dict[str, Any]:
        """Find documentation for a library/framework.

        Args:
            library: e.g., "FastAPI", "React", "Prisma"
            topic: e.g., "dependency injection", "hooks", "migrations"

        Returns:
            {"findings": [...], "summary": str, "sources": [...]}
        """
        query = (
            f"Official documentation and best practices for {library} {topic}.\n"
            f"Show the recommended approach with code examples."
        )

        return await self._search(query, "documentation")

    async def research_best_practices(
        self,
        topic: str,
        framework: str = "",
    ) -> dict[str, Any]:
        """Research patterns and best practices.

        Args:
            topic: e.g., "JWT authentication", "rate limiting"
            framework: e.g., "FastAPI", "Express"

        Returns:
            {"findings": [...], "summary": str, "sources": [...]}
        """
        query = (
            f"Best practices and recommended patterns for {topic}"
            f"{f' in {framework}' if framework else ''}.\n"
            f"Include production-ready code examples and common pitfalls."
        )

        return await self._search(query, "best_practices")

    async def _search(
        self,
        query: str,
        search_type: str,
    ) -> dict[str, Any]:
        """Core search method using Gemini with grounding."""
        try:
            from app.agents.base import call_ai

            response = await call_ai(
                self,
                messages=[{"role": "user", "content": query}],
                system_prompt=(
                    "You are a research agent. Search the web and provide "
                    "accurate, up-to-date information with specific code examples. "
                    "Always cite your sources. Be concise and actionable."
                ),
                task_type="research",
                temperature=0.3,
                max_tokens=2000,
            )

            return {
                "findings": [response.content],
                "summary": response.content[:500],
                "sources": [],  # Gemini grounding provides inline sources
                "model_used": response.model_used,
                "search_type": search_type,
            }

        except Exception as exc:
            logger.warning(
                "research_search_failed",
                search_type=search_type,
                error=str(exc)[:200],
            )
            return {
                "findings": [],
                "summary": f"Research failed: {str(exc)[:100]}",
                "sources": [],
                "search_type": search_type,
                "error": str(exc)[:200],
            }
