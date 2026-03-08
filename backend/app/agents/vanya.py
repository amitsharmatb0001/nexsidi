"""Vanya — UI/UX Designer: design specs, tokens, and wireframes.

Vanya produces design specifications BEFORE code generation, so that
Shubham (backend) and Aanya (frontend) have a consistent visual language.

REVIEW-FIX: Converted from single-shot call_ai() to agentic call_ai_with_tools()
with self-validation tools. Vanya can now validate design tokens (contrast ratios,
spacing scale), validate page specs, and iteratively refine before finalizing.

Output:
- Design tokens (colors, fonts, spacing, borders, shadows)
- Page wireframe descriptions (layout, components, interactions)
- Component specifications (props, variants, responsive behavior)
- Accessibility requirements per component
"""

from __future__ import annotations

import json as _json
import re as _re
from typing import Any

import structlog

from app.agents.base import (
    AgentResult,
    AgentStatus,
    ToolDefinition,
    call_ai_with_tools,
    check_inbox,
    format_inbox_for_prompt,
    notify_agents,
    register_agent,
    run_agent,
    store_output,
)
from app.services.ai_router import TaskComplexity

logger = structlog.get_logger(__name__)


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int] | None:
    """Convert hex color to RGB tuple."""
    hex_color = hex_color.strip().lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    if len(hex_color) != 6 or not all(c in "0123456789abcdefABCDEF" for c in hex_color):
        return None
    return (int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16))


def _relative_luminance(r: int, g: int, b: int) -> float:
    """Calculate relative luminance per WCAG 2.1."""
    def _linearize(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4
    return 0.2126 * _linearize(r) + 0.7152 * _linearize(g) + 0.0722 * _linearize(b)


def _contrast_ratio(rgb1: tuple[int, int, int], rgb2: tuple[int, int, int]) -> float:
    """Calculate WCAG contrast ratio between two colors."""
    l1 = _relative_luminance(*rgb1)
    l2 = _relative_luminance(*rgb2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


class VanyaToolHandler:
    """Handles tool calls for Vanya's agentic design spec loop."""

    def __init__(self) -> None:
        self._design_spec: dict[str, Any] | None = None
        self._tokens_validated: bool = False
        self._spec_validated: bool = False
        self._complete: bool = False

    async def __call__(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "validate_tokens":
            return self._validate_tokens(tool_input["tokens_json"])
        elif tool_name == "validate_page_specs":
            return self._validate_page_specs(tool_input["page_specs_json"])
        elif tool_name == "write_design_spec":
            return self._write_design_spec(tool_input["spec"])
        else:
            return f"Unknown tool: {tool_name}"

    def _validate_tokens(self, tokens_json: str) -> str:
        """Validate design tokens: check colors, contrast, spacing scale."""
        try:
            tokens = _json.loads(tokens_json)
        except _json.JSONDecodeError as e:
            return f"Validation FAILED: invalid JSON — {e}"

        errors: list[str] = []
        warnings: list[str] = []

        # -- Colors validation --
        colors = tokens.get("colors", {})
        if not isinstance(colors, dict):
            errors.append("'colors' must be an object")
        else:
            required_colors = [
                "primary", "secondary", "background", "surface",
                "text_primary", "text_secondary", "error", "success",
            ]
            for color_name in required_colors:
                val = colors.get(color_name)
                if not val:
                    errors.append(f"Missing color: '{color_name}'")
                elif not isinstance(val, str) or not _re.match(r"^#[0-9a-fA-F]{3,8}$", val.strip()):
                    errors.append(f"Color '{color_name}' must be a valid hex (got '{val}')")

            # WCAG contrast checks
            bg_rgb = _hex_to_rgb(colors.get("background", ""))
            text_rgb = _hex_to_rgb(colors.get("text_primary", ""))
            # AUDIT-T2-10: Warn when hex is invalid instead of silently skipping contrast check
            if colors.get("background") and not bg_rgb:
                warnings.append(f"Invalid hex color for 'background': '{colors.get('background')}' — contrast check skipped")
            if colors.get("text_primary") and not text_rgb:
                warnings.append(f"Invalid hex color for 'text_primary': '{colors.get('text_primary')}' — contrast check skipped")
            if bg_rgb and text_rgb:
                ratio = _contrast_ratio(bg_rgb, text_rgb)
                if ratio < 4.5:
                    errors.append(
                        f"WCAG AA FAIL: text_primary on background has contrast "
                        f"{ratio:.1f}:1 (need >= 4.5:1)"
                    )
                elif ratio < 7.0:
                    warnings.append(
                        f"WCAG AAA: text_primary on background has contrast "
                        f"{ratio:.1f}:1 (AAA needs >= 7:1)"
                    )

            text2_rgb = _hex_to_rgb(colors.get("text_secondary", ""))
            if colors.get("text_secondary") and not text2_rgb:
                warnings.append(f"Invalid hex color for 'text_secondary': '{colors.get('text_secondary')}' — contrast check skipped")
            if bg_rgb and text2_rgb:
                ratio = _contrast_ratio(bg_rgb, text2_rgb)
                if ratio < 4.5:
                    errors.append(
                        f"WCAG AA FAIL: text_secondary on background has contrast "
                        f"{ratio:.1f}:1 (need >= 4.5:1)"
                    )

        # -- Typography validation --
        typo = tokens.get("typography", {})
        if not isinstance(typo, dict):
            errors.append("'typography' must be an object")
        else:
            if not typo.get("font_family"):
                errors.append("Typography missing 'font_family'")

        # -- Spacing validation --
        spacing = tokens.get("spacing", {})
        if not isinstance(spacing, dict):
            errors.append("'spacing' must be an object")
        else:
            required_spacing = ["xs", "sm", "md", "lg", "xl"]
            for sp in required_spacing:
                if sp not in spacing:
                    errors.append(f"Spacing missing '{sp}'")

        result_parts = []
        if errors:
            result_parts.append("Validation FAILED:\n" + "\n".join(f"- {e}" for e in errors))
        if warnings:
            result_parts.append("Warnings:\n" + "\n".join(f"- {w}" for w in warnings))
        if not errors:
            self._tokens_validated = True
            result_parts.append("Design tokens validation PASSED")

        return "\n\n".join(result_parts)

    def _validate_page_specs(self, page_specs_json: str) -> str:
        """Validate page specifications structure."""
        try:
            specs = _json.loads(page_specs_json)
        except _json.JSONDecodeError as e:
            return f"Validation FAILED: invalid JSON — {e}"

        if not isinstance(specs, list):
            return "Validation FAILED: page_specs must be an array"

        if len(specs) == 0:
            return "Validation FAILED: page_specs is empty — at least 1 page required"

        errors: list[str] = []
        for i, page in enumerate(specs):
            if not isinstance(page, dict):
                errors.append(f"Page [{i}]: must be an object")
                continue
            if not page.get("name"):
                errors.append(f"Page [{i}]: missing 'name'")
            if not page.get("route"):
                errors.append(f"Page [{i}]: missing 'route'")
            if not page.get("layout_type"):
                errors.append(f"Page [{i}]: missing 'layout_type'")
            sections = page.get("sections", [])
            if not isinstance(sections, list) or len(sections) == 0:
                errors.append(f"Page [{i}] '{page.get('name', '?')}': missing or empty 'sections'")

        if errors:
            return "Validation FAILED:\n" + "\n".join(f"- {e}" for e in errors)

        self._spec_validated = True
        return f"Page specs validation PASSED ({len(specs)} pages verified)"

    def _write_design_spec(self, spec: str) -> str:
        """Finalize and store the design specification."""
        try:
            data = _json.loads(spec) if isinstance(spec, str) else spec
        except _json.JSONDecodeError as e:
            return f"Error: invalid JSON in write_design_spec — {e}"

        if not self._tokens_validated:
            return "Error: call validate_tokens first to ensure design tokens are correct"

        if not self._spec_validated:
            return "Error: call validate_page_specs first to ensure page specs are correct"

        self._design_spec = data
        self._complete = True
        return "Design specification written successfully. Task complete."


class Vanya:
    """UI/UX Designer — design tokens and wireframe specifications."""

    name = "vanya"
    display_name = "Vanya — UI/UX Designer"
    default_complexity = TaskComplexity.MEDIUM
    default_model: str | None = None

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

        self.register_tool(ToolDefinition(
            name="validate_tokens",
            description=(
                "Validate design tokens JSON for completeness and WCAG compliance. "
                "Checks that all required colors exist and are valid hex values, "
                "verifies WCAG 2.1 AA contrast ratios (text on background >= 4.5:1), "
                "checks typography and spacing scale completeness. "
                "ALWAYS call this before write_design_spec."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tokens_json": {
                        "type": "string",
                        "description": "JSON string containing design_tokens object.",
                    },
                },
                "required": ["tokens_json"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="validate_page_specs",
            description=(
                "Validate page specifications for structural correctness. "
                "Checks that each page has name, route, layout_type, and sections. "
                "ALWAYS call this before write_design_spec."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "page_specs_json": {
                        "type": "string",
                        "description": "JSON string containing page_specs array.",
                    },
                },
                "required": ["page_specs_json"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="write_design_spec",
            description=(
                "Finalize and store the complete design specification. "
                "Call AFTER both validate_tokens and validate_page_specs pass. "
                "Signals task completion."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "spec": {
                        "type": "string",
                        "description": (
                            "The complete design spec JSON with keys: "
                            "design_tokens, page_specs, component_specs."
                        ),
                    },
                },
                "required": ["spec"],
            },
        ))

    def register_tool(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    @property
    def tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

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
        # PHASE-3: Check inbox for messages from other agents
        inbox_messages = await check_inbox(self.name, pipeline_run_id)
        inbox_context = format_inbox_for_prompt(inbox_messages)

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
            "Ensure WCAG 2.1 AA contrast ratios.\n\n"
            "## WORKFLOW (use tools in this order):\n"
            "1. Generate design tokens with WCAG-compliant colors\n"
            "2. Call validate_tokens to verify contrast ratios and completeness\n"
            "3. If validation fails, fix the issues and re-validate\n"
            "4. Generate page specs for all pages\n"
            "5. Call validate_page_specs to verify structure\n"
            "6. If validation fails, fix and re-validate\n"
            "7. Once both validations pass, call write_design_spec with the "
            "complete spec (design_tokens + page_specs + component_specs)"
        )

        # PHASE-10: Enrich prompt with learned knowledge, lessons, and warnings
        try:
            from app.services.dynamic_prompt_builder import get_dynamic_prompt_builder
            _dpb = get_dynamic_prompt_builder()
            _frontend_fw = contract.get("tech_stack", {}).get("frontend", "")
            system_prompt = await _dpb.build_system_prompt(
                agent_name=self.name,
                task_context={
                    "task_type": "ui_design",
                    "framework": _frontend_fw,
                    "task_summary": f"Design UI/UX for {project_name} ({len(pages)} pages)",
                    "previous_agent_outputs": {
                        "vikram": f"Contract with {len(pages)} pages",
                    },
                },
                base_prompt_fallback=system_prompt,
            )
        except Exception as _dpb_exc:
            logger.debug("dynamic_prompt_fallback", agent=self.name, error=str(_dpb_exc)[:100])

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

        # PHASE-3: Inject inbox messages into prompt context
        if inbox_context:
            user_content = f"{inbox_context}\n\n{user_content}"
            # AUTHORITY directives from Tilotma/Vikram override normal flow
            if "AUTHORITY" in inbox_context:
                system_prompt += (
                    "\n\n⚠ AUTHORITY DIRECTIVE RECEIVED — you MUST comply:\n"
                    + inbox_context
                )

        # FIX-40: Inject rejected approaches
        from app.agents.base import build_rejection_context
        _rejection_ctx = build_rejection_context(context)
        if _rejection_ctx:
            user_content += _rejection_ctx

        handler = VanyaToolHandler()

        try:
            response = await call_ai_with_tools(
                agent=self,
                messages=[{"role": "user", "content": user_content}],
                system_prompt=system_prompt,
                task_type="general",
                tool_handler=handler,
                max_tool_rounds=10,
            )
        except Exception as exc:
            # R21-FIX: Sanitize exception to prevent API key leakage.
            from app.services.ai_router import _sanitize_error
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error=f"AI call failed: {_sanitize_error(exc)}",
            )

        # Use tool handler's validated spec if available
        design_spec = handler._design_spec or response.content

        output = {
            "design_spec": design_spec,
            "tokens_validated": handler._tokens_validated,
            "spec_validated": handler._spec_validated,
            "page_count": len(pages),
            "model_used": response.model_used,
            "tokens": {
                "input": response.input_tokens,
                "output": response.output_tokens,
            },
        }

        # ── LLM self-evaluation: design completeness ──
        try:
            llm_eval = await self._run_llm_self_evaluation(
                design_spec, pages, context,
            )
            output["llm_evaluation"] = llm_eval
        except Exception:
            logger.warning("vanya_self_eval_failed", exc_info=True)

        await store_output(self, pipeline_run_id, output)

        # PHASE-3: Notify agents that design specs are ready
        await notify_agents(
            self.name, pipeline_run_id,
            f"Design specs complete for {project_name} ({len(pages)} pages). "
            f"Tokens validated: {handler._tokens_validated}, Spec validated: {handler._spec_validated}.",
        )

        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.COMPLETED,
            output=output,
            model_used=response.model_used,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )

    # ── LLM-driven self-evaluation ──────────────────────────────────

    async def _run_llm_self_evaluation(
        self,
        design_spec: Any,
        contract_pages: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """LLM reviews its own design specs for completeness.

        Checks: all contract pages have design specs, design tokens consistent,
        responsive breakpoints defined, accessibility requirements, interactive elements.
        Uses cheapest model (~$0.002/call).
        """
        import json as json_mod

        # Summarize what was designed
        if isinstance(design_spec, dict):
            spec_text = json_mod.dumps(design_spec, indent=2, default=str)[:3000]
        else:
            spec_text = str(design_spec)[:3000]

        # Contract pages
        page_lines = []
        for p in contract_pages[:15]:
            name = p.get("name", p.get("title", "unknown"))
            components = p.get("components", [])
            comp_str = f" — {len(components)} components" if components else ""
            page_lines.append(f"  {name}{comp_str}")
        pages_text = "\n".join(page_lines) if page_lines else "  (no pages in contract)"

        eval_prompt = (
            "You are reviewing UI/UX design specs YOU just produced. Be brutally honest.\n\n"
            f"## Contract Pages Required ({len(contract_pages)} total)\n{pages_text}\n\n"
            f"## Your Design Spec\n{spec_text}\n\n"
            "## Your Task\n"
            "Compare what the contract REQUIRES vs what you DESIGNED:\n"
            "1. Did you design ALL pages from the contract? List any missing.\n"
            "2. Are design tokens consistent across pages (colors, fonts, spacing)?\n"
            "3. Did you define responsive breakpoints (mobile, tablet, desktop)?\n"
            "4. Did you specify accessibility (WCAG) requirements per component?\n"
            "5. Did you define all interactive elements (buttons, forms, modals)?\n"
            "6. Did you define navigation flow between pages?\n\n"
            "Respond in JSON:\n"
            "{\n"
            '  "missing_pages": ["page1", "page2"],\n'
            '  "missing_components": [{"page": "name", "component": "missing"}],\n'
            '  "token_issues": ["inconsistent colors", ...],\n'
            '  "accessibility_gaps": ["no contrast ratio defined", ...],\n'
            '  "completeness_pct": 0-100,\n'
            '  "verdict": "PASS" or "FAIL",\n'
            '  "reasoning": "brief explanation"\n'
            "}\n"
        )

        from app.services.ai_router import get_ai_router, AIRequest, AIMessage
        from app.agents.base import TaskComplexity

        router = get_ai_router()
        resp = await router.call(AIRequest(
            messages=[AIMessage(role="user", content=eval_prompt)],
            complexity=TaskComplexity.LOW,
            max_tokens=1000,
            agent_name=f"{self.name}_self_eval",
        ))

        from app.utils.json_parser import parse_json
        result = parse_json(resp.content, fallback={})
        if not isinstance(result, dict):
            result = {}
        return result


# Register the agent
_vanya = Vanya()
register_agent(_vanya)
