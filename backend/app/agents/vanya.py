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
    BaseAgent,
    ToolDefinition,
    register_agent,
)
from app.services.ai_router import TaskComplexity

logger = structlog.get_logger(__name__)


class Vanya(BaseAgent):
    """UI/UX Designer — design tokens and wireframe specifications."""

    name = "vanya"
    display_name = "Vanya — UI/UX Designer"
    default_complexity = TaskComplexity.MEDIUM

    def __init__(self) -> None:
        super().__init__()

        self.register_tool(ToolDefinition(
            name="write_design_spec",
            description="Write a design specification document.",
            parameters={
                "type": "object",
                "properties": {
                    "component": {"type": "string"},
                    "spec": {"type": "object"},
                },
                "required": ["component", "spec"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="generate_wireframe_description",
            description="Generate a text-based wireframe description for a page.",
            parameters={
                "type": "object",
                "properties": {
                    "page_name": {"type": "string"},
                    "layout": {"type": "string", "description": "Layout description."},
                    "components": {"type": "array", "items": {"type": "string"}},
                    "responsive_notes": {"type": "string"},
                },
                "required": ["page_name", "layout"],
            },
        ))

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

        user_content = (
            f"## Project: {project_name}\n\n"
            f"## Pages from Architecture Contract\n```json\n{pages_json}\n```"
        )

        try:
            response = await self.call_ai(
                messages=[{"role": "user", "content": user_content}],
                system_prompt=system_prompt,
                task_type="general",
                temperature=0.5,  # Moderate creativity for design
            )
        except Exception as exc:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error=f"AI call failed: {exc}",
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

        await self.store_output(pipeline_run_id, output)

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
