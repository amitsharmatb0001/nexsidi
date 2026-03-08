"""Dynamic Prompt Builder — AI-driven prompt construction.

PHASE-10: Replaces ALL hardcoded agent prompt strings with dynamically
constructed prompts assembled from:
  1. Base role templates (from PromptEngine — Valkey-cached, evolvable)
  2. Recalled knowledge (from AgentMemory — ChromaDB vector search)
  3. Mistake lessons (from MistakeMemory — what NOT to do)
  4. Framework-specific rules (from CapabilityRegistry)
  5. Project-specific context (from previous agent outputs)
  6. Proactive error pattern warnings (from MistakeMemory analysis)

No agent should have hardcoded prompt strings after this phase.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class DynamicPromptBuilder:
    """Builds agent prompts dynamically from multiple intelligence sources."""

    def __init__(self) -> None:
        # Lazy-loaded dependencies to avoid circular imports
        self._memory = None
        self._mistakes = None
        self._prompts = None
        self._capabilities = None

    def _get_agent_memory(self) -> Any:
        if self._memory is None:
            try:
                from app.services.agent_memory import get_agent_memory
                self._memory = get_agent_memory()
            except Exception:
                self._memory = None
        return self._memory

    def _get_mistake_memory(self) -> Any:
        if self._mistakes is None:
            try:
                from app.services.mistake_memory import mistake_memory
                self._mistakes = mistake_memory
            except Exception:
                self._mistakes = None
        return self._mistakes

    def _get_prompt_engine(self) -> Any:
        if self._prompts is None:
            try:
                from app.services.prompt_engine import get_prompt_engine
                self._prompts = get_prompt_engine()
            except Exception:
                self._prompts = None
        return self._prompts

    def _get_capability_registry(self) -> Any:
        if self._capabilities is None:
            try:
                from app.services import capability_registry
                self._capabilities = capability_registry
            except Exception:
                self._capabilities = None
        return self._capabilities

    async def build_system_prompt(
        self,
        agent_name: str,
        task_context: dict[str, Any],
        base_prompt_fallback: str = "",
    ) -> str:
        """ENRICH (never replace) the agent's detailed system prompt.

        The base_prompt_fallback is the agent's carefully crafted 50-200 line
        prompt with all instructions, golden examples, and rules. It is ALWAYS
        used as the foundation. We APPEND intelligence sections on top:
        - Recalled knowledge from past projects
        - Mistake lessons (what NOT to do)
        - Framework-specific rules
        - Context from other agents
        - Proactive error pattern warnings

        Args:
            agent_name: Name of the agent (e.g., "shubham", "aanya")
            task_context: Dict with task_type, framework, task_summary, etc.
            base_prompt_fallback: The agent's full detailed prompt — ALWAYS used.

        Returns:
            Enriched system prompt = original + intelligence supplements.
        """
        # FOUNDATION: The agent's detailed prompt is ALWAYS the base.
        # Never replace it — only enrich it with learned intelligence.
        parts: list[str] = []
        if base_prompt_fallback:
            parts.append(base_prompt_fallback)

        # Supplementary intelligence sections (appended, never replacing)
        enrichments: list[str] = []

        # 1. Recalled knowledge from past projects
        knowledge_section = await self._get_recalled_knowledge(
            agent_name, task_context.get("task_summary", "")
        )
        if knowledge_section:
            enrichments.append(knowledge_section)

        # 2. Mistake lessons — what NOT to do
        lessons_section = self._get_mistake_lessons(
            agent_name,
            task_context.get("task_type", ""),
            task_context.get("task_summary", ""),
        )
        if lessons_section:
            enrichments.append(lessons_section)

        # 3. Framework-specific rules from capability registry
        framework_section = self._get_framework_rules(task_context.get("framework"))
        if framework_section:
            enrichments.append(framework_section)

        # 4. Project-specific context from other agents
        context_section = self._get_agent_context(
            task_context.get("previous_agent_outputs", {})
        )
        if context_section:
            enrichments.append(context_section)

        # 5. Proactive error pattern warnings
        warnings_section = self._get_error_warnings(agent_name)
        if warnings_section:
            enrichments.append(warnings_section)

        # Only append enrichments if we have any
        if enrichments:
            parts.append("\n\n--- DYNAMIC INTELLIGENCE (learned from past projects) ---")
            parts.extend(enrichments)

        prompt = "\n\n".join(parts)

        logger.info(
            "dynamic_prompt_built",
            agent=agent_name,
            base_chars=len(base_prompt_fallback),
            enrichment_sections=len(enrichments),
            total_chars=len(prompt),
            has_knowledge=bool(knowledge_section),
            has_lessons=bool(lessons_section),
            has_framework=bool(framework_section),
            has_warnings=bool(warnings_section),
        )

        return prompt

    async def _get_recalled_knowledge(
        self, agent_name: str, query: str
    ) -> str:
        """Recall relevant knowledge from past projects via vector search."""
        memory = self._get_agent_memory()
        if not memory or not query:
            return ""

        try:
            results = memory.recall_knowledge(agent_name, query, n_results=5)
            if not results:
                return ""

            lines = ["## Learned Knowledge from Past Projects"]
            for item in results:
                topic = item.get("topic", "unknown")
                content = item.get("content", "")
                if content:
                    lines.append(f"- **{topic}**: {content[:300]}")

            return "\n".join(lines) if len(lines) > 1 else ""
        except Exception as exc:
            logger.debug("recall_knowledge_failed", agent=agent_name, error=str(exc)[:100])
            return ""

    def _get_mistake_lessons(
        self, agent_name: str, task_type: str, task_summary: str
    ) -> str:
        """Get mistake lessons from MistakeMemory."""
        mistakes = self._get_mistake_memory()
        if not mistakes:
            return ""

        try:
            lessons = mistakes.build_lessons_prompt(agent_name, task_type, task_summary)
            return lessons or ""
        except Exception:
            return ""

    def _get_framework_rules(self, framework: str | None) -> str:
        """Get framework-specific rules from CapabilityRegistry."""
        if not framework:
            return ""

        registry = self._get_capability_registry()
        if not registry:
            return ""

        try:
            cap = registry.get_capability(framework)
            if not cap or not cap.rules:
                return ""

            lines = [f"## {cap.name} ({cap.language}) — Specific Rules"]
            for rule in cap.rules:
                lines.append(f"- {rule}")
            return "\n".join(lines)
        except Exception:
            return ""

    def _get_agent_context(
        self, previous_outputs: dict[str, str]
    ) -> str:
        """Summarize what other agents have done so far."""
        if not previous_outputs:
            return ""

        lines = ["## Context from Other Agents"]
        for agent, summary in previous_outputs.items():
            if summary:
                lines.append(f"### {agent}\n{summary[:500]}")

        return "\n".join(lines) if len(lines) > 1 else ""

    def _get_error_warnings(self, agent_name: str) -> str:
        """Get proactive error pattern warnings."""
        mistakes = self._get_mistake_memory()
        if not mistakes:
            return ""

        try:
            patterns = mistakes.analyze_error_patterns(agent_name)
            if not patterns:
                return ""

            lines = ["## PROACTIVE WARNINGS (from past error patterns)"]
            for p in patterns[:5]:
                pct = p.get("pct", 0)
                error_type = p.get("type", "unknown")
                example = p.get("example", "")[:100]
                lines.append(
                    f"- **{error_type}** occurs {pct:.0%} of the time. "
                    f"Example: {example}"
                )
            return "\n".join(lines)
        except Exception:
            return ""

    async def store_learning_on_success(
        self, agent_name: str, task_type: str, output: dict[str, Any]
    ) -> None:
        """After successful agent completion, extract and store useful patterns."""
        memory = self._get_agent_memory()
        if not memory:
            return

        try:
            # Extract key patterns from output
            file_contents = output.get("file_contents", {})
            if not file_contents:
                return

            # Store aggregate knowledge about what worked
            framework = output.get("framework", "unknown")
            file_count = len(file_contents)
            memory.store_knowledge(
                agent_name,
                f"{framework}_generation_success",
                f"Successfully generated {file_count} files for {framework}. "
                f"Task: {task_type}.",
                metadata={"task_type": task_type, "framework": framework},
            )

            logger.info(
                "learning_stored_on_success",
                agent=agent_name,
                task_type=task_type,
                framework=framework,
                files=file_count,
            )
        except Exception as exc:
            logger.debug("store_learning_failed", agent=agent_name, error=str(exc)[:100])

    async def evolve_template(
        self, agent_name: str, performance_data: dict[str, Any]
    ) -> None:
        """After pipeline completes, review and potentially update templates.

        This is the self-improvement loop — templates evolve based on results.
        Currently stores performance data for future analysis. Full template
        evolution requires more data (multiple projects) before modifying templates.
        """
        memory = self._get_agent_memory()
        if not memory:
            return

        try:
            success = performance_data.get("success", False)
            errors = performance_data.get("errors_found", 0)
            fixed = performance_data.get("errors_fixed", 0)

            memory.store_knowledge(
                agent_name,
                f"template_performance_{agent_name}",
                f"Success={success}, errors_found={errors}, errors_fixed={fixed}. "
                f"Framework: {performance_data.get('framework', 'unknown')}.",
                metadata=performance_data,
            )
        except Exception:
            pass  # Non-critical — error logged upstream or handled by caller


# ── Singleton ────────────────────────────────────────────────────────

_prompt_builder: DynamicPromptBuilder | None = None


def get_dynamic_prompt_builder() -> DynamicPromptBuilder:
    """Get or create the singleton DynamicPromptBuilder."""
    global _prompt_builder
    if _prompt_builder is None:
        _prompt_builder = DynamicPromptBuilder()
    return _prompt_builder
