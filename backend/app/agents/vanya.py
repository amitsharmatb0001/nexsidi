"""Vanya — UI/UX Designer: design specs, tokens, and wireframes.

Vanya produces design specifications BEFORE code generation, so that
Shubham (backend) and Aanya (frontend) have a consistent visual language.

Output:
- Design tokens (colors, fonts, spacing, borders, shadows)
- Page wireframe descriptions (layout, components, interactions)
- Component specifications (props, variants, responsive behavior)
- Accessibility requirements per component
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.base import (
    AgentResult,
    AgentStatus,
    call_ai,
    register_agent,
    run_agent,
    store_output,
)
from app.services.ai_router import TaskComplexity

logger = structlog.get_logger(__name__)


class Vanya:
    """UI/UX Designer — design tokens and wireframe specifications."""

    name = "vanya"
    display_name = "Vanya — UI/UX Designer"
    default_complexity = TaskComplexity.MEDIUM
    default_model: str | None = None

    @property
    def tools(self) -> list:
        """No tools — Vanya is a single-shot design spec generator."""
        return []

    async def run(
        self,
        pipeline_run_id: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Execute with timing, logging, and error handling."""
        return await run_agent(self, pipeline_run_id, context)

    async def execute(
        self,
        pipeline_run_id: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Generate UI/UX design specs from architecture contract."""
        vikram_output = context.get("vikram")
        if not vikram_output:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error="No architecture contract from Vikram",
            )

        contract = vikram_output.get("contract", {})
        pages = contract.get("frontend", {}).get("pages", [])
        project_name = contract.get("project_name", "Project")

        system_prompt = (
            "You are Vanya, the UI/UX Designer at NexSidi. Generate a complete "
            "design specification based on the architecture contract.\n\n"
            "Output JSON with:\n"
            "1. design_tokens: {\n"
            "   colors: {primary, secondary, accent, background, surface, "
            "text_primary, text_secondary, error, success, warning},\n"
            "   typography: {font_family, heading_sizes, body_size, line_height},\n"
            "   spacing: {xs, sm, md, lg, xl, xxl},\n"
            "   borders: {radius_sm, radius_md, radius_lg, width},\n"
            "   shadows: {sm, md, lg}\n"
            "}\n"
            "2. page_specs: array of {\n"
            "   name, route, layout_type (sidebar/full/split/modal),\n"
            "   sections: [{name, components, grid_area}],\n"
            "   responsive: {mobile, tablet, desktop notes},\n"
            "   accessibility: [WCAG requirements]\n"
            "}\n"
            "3. component_specs: array of {\n"
            "   name, description, props, variants,\n"
            "   responsive_behavior, accessibility_role\n"
            "}\n\n"
            "Design should be modern, clean, and professional. "
            "Use a neutral color palette with one accent color. "
            "Ensure WCAG 2.1 AA contrast ratios."
        )

        import orjson
        pages_json = orjson.dumps(pages).decode("utf-8")

        # AUDIT-FIX: Wrap contract-derived data in XML delimiters for defense-in-depth.
        user_content = (
            "<project_context>\n"
            f"## Project: {project_name}\n\n"
            f"## Pages to Design\n{pages_json}\n"
            "</project_context>\n\n"
            "Generate the UI/UX design specification for the pages described above."
        )

        try:
            response = await call_ai(self, 
                messages=[{"role": "user", "content": user_content}],
                system_prompt=system_prompt,
                task_type="general",
                temperature=0.5,  # Moderate creativity for design
            )
        except Exception as exc:
            # R21-FIX: Sanitize exception to prevent API key leakage.
            from app.services.ai_router import _sanitize_error
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error=f"AI call failed: {_sanitize_error(exc)}",
            )

        output = {
            "design_spec": response.content,
            "page_count": len(pages),
            "model_used": response.model_used,
            "tokens": {
                "input": response.input_tokens,
                "output": response.output_tokens,
            },
        }

        await store_output(self, pipeline_run_id, output)

        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.COMPLETED,
            output=output,
            model_used=response.model_used,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )


# Register the agent
_vanya = Vanya()
register_agent(_vanya)
