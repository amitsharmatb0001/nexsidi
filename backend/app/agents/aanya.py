"""Aanya — Frontend Developer: hybrid template + AI code generation.

Aanya generates frontend code using a three-phase approach:
1. Template phase (ZERO AI): package.json, tsconfig.json, tailwind.config.js
2. Auto-generated (ZERO AI): types/index.ts (from Shubham's schemas),
   api/client.ts (from contract endpoints)
3. AI phase (ordered):
   AuthContext → Layout → Pages → App.tsx (generated LAST with real paths)

Frontend types MATCH backend types exactly because they are auto-generated
from the same architecture contract.
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

# Dependency-ordered file generation sequence for frontend
FRONTEND_GENERATION_ORDER: list[dict[str, str]] = [
    {"name": "auth_context", "path": "frontend/src/contexts/AuthContext.tsx",
     "description": "Authentication context provider (login state, token management)"},
    {"name": "layout", "path": "frontend/src/components/Layout.tsx",
     "description": "Main app layout (sidebar, header, content area)"},
    {"name": "pages", "path": "frontend/src/pages/",
     "description": "All page components from contract frontend.pages"},
    {"name": "app", "path": "frontend/src/app/page.tsx",
     "description": "Root page with routing — generated LAST with real import paths"},
]


class Aanya(BaseAgent):
    """Frontend Developer — hybrid template + AI code generation."""

    name = "aanya"
    display_name = "Aanya — Frontend Developer"
    default_complexity = TaskComplexity.HIGH

    def __init__(self) -> None:
        super().__init__()

        self.register_tool(ToolDefinition(
            name="write_file",
            description="Write a generated code file to the project.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "language": {"type": "string", "enum": ["typescript", "tsx", "css", "json"]},
                },
                "required": ["path", "content"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="read_file",
            description="Read a previously generated file for context.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="read_contract",
            description="Read the architecture contract.",
            parameters={
                "type": "object",
                "properties": {},
            },
        ))

    async def execute(
        self,
        pipeline_run_id: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Generate frontend code from approved architecture contract.

        Phase 1: Template engine generates config files (zero AI).
        Phase 2: Auto-generate types + API client (zero AI).
        Phase 3: AI generates components in dependency order.
        """
        vikram_output = context.get("vikram")
        if not vikram_output:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error="No architecture contract from Vikram",
            )

        contract = vikram_output.get("contract", {})
        vanya_output = context.get("vanya", {})
        generated_files: dict[str, str] = {}

        # ── Phase 1: Template scaffolding (ZERO AI) ──
        try:
            from app.engine.template_engine import get_template_engine

            engine = get_template_engine()
            template_files = engine.render_all(contract, categories=["frontend"])

            for f in template_files:
                generated_files[f.path] = f.content

            logger.info(
                "template_phase_complete",
                files_generated=len(template_files),
            )
        except Exception as exc:
            logger.warning("template_phase_failed", error=str(exc))

        # ── Phase 2: AI component generation (ordered) ──
        accumulated_code: dict[str, str] = {}

        # Include auto-generated types + API client as context
        for path, content in generated_files.items():
            if "types/index.ts" in path or "api-client.ts" in path:
                name = path.split("/")[-1].replace(".ts", "").replace("-", "_")
                accumulated_code[name] = content

        for step in FRONTEND_GENERATION_ORDER:
            system_prompt = self._build_generation_prompt(
                step=step,
                contract=contract,
                accumulated_code=accumulated_code,
                design_spec=vanya_output.get("design_spec", ""),
            )

            try:
                response = await self.call_ai(
                    messages=[{
                        "role": "user",
                        "content": f"Generate the {step['description']} for this project.",
                    }],
                    system_prompt=system_prompt,
                    task_type="general",
                    temperature=0.2,
                )

                accumulated_code[step["name"]] = response.content
                generated_files[step["path"]] = response.content

                logger.info(
                    "ai_generation_step",
                    step=step["name"],
                    model=response.model_used,
                    tokens=response.output_tokens,
                )

            except Exception as exc:
                logger.error("ai_generation_failed", step=step["name"], error=str(exc))
                accumulated_code[step["name"]] = f"// Generation failed: {exc}"

        output = {
            "generated_files": list(generated_files.keys()),
            "template_files_count": len([f for f in generated_files if "package.json" in f or "tsconfig" in f]),
            "ai_files": [s["path"] for s in FRONTEND_GENERATION_ORDER],
            "file_count": len(generated_files),
            "generation_order": [s["name"] for s in FRONTEND_GENERATION_ORDER],
        }

        await self.store_output(pipeline_run_id, output)

        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.COMPLETED,
            output=output,
        )

    def _build_generation_prompt(
        self,
        step: dict[str, str],
        contract: dict[str, Any],
        accumulated_code: dict[str, str],
        design_spec: str,
    ) -> str:
        """Build context-rich prompt for frontend generation."""
        import orjson

        pages = contract.get("frontend", {}).get("pages", [])
        pages_json = orjson.dumps(pages, option=orjson.OPT_INDENT_2).decode("utf-8")

        prompt_parts = [
            "You are Aanya, the Frontend Developer at NexSidi.",
            f"Generate the {step['description']}.",
            "",
            "## Frontend Pages (from Architecture Contract)",
            f"```json\n{pages_json}\n```",
            "",
        ]

        # Design specs from Vanya
        if design_spec and step["name"] in ("layout", "pages"):
            prompt_parts.extend([
                "## Design Specification (from Vanya)",
                str(design_spec),
                "",
            ])

        # Previously generated code
        if accumulated_code:
            prompt_parts.append("## Previously Generated Files (use exact import paths)")
            for name, code in accumulated_code.items():
                lang = "tsx" if "tsx" in name or "context" in name.lower() else "typescript"
                prompt_parts.append(f"\n### {name}\n```{lang}\n{code}\n```")
            prompt_parts.append("")

        # Rules
        prompt_parts.extend([
            "## MANDATORY RULES",
            "1. Use Next.js 15 App Router with React 19",
            "2. Use TypeScript strict mode — no 'any' types",
            "3. Import types from '@/types' (auto-generated, don't reinvent)",
            "4. Import API functions from '@/lib/api-client' (auto-generated)",
            "5. NEVER use 'TODO', placeholder text, or stub functions",
            "6. EVERY component must be fully functional",
            "7. Use Tailwind CSS for styling (no inline styles, no CSS modules)",
            "8. Responsive: mobile-first (375px, 768px, 1280px breakpoints)",
            "9. Accessibility: semantic HTML, ARIA labels, keyboard navigation",
            "10. Output ONLY the code — no markdown, no explanation",
        ])

        return "\n".join(prompt_parts)


# Register the agent
_aanya = Aanya()
register_agent(_aanya)
