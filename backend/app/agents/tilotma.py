"""Tilotma — Project Manager Agent: conversational requirements gathering.

Tilotma is the first agent in the pipeline. She conducts a structured
conversation with the user to extract project requirements.

Capabilities:
- Multi-turn conversation (text, voice transcripts, image descriptions)
- Auto-detects compliance needs (DPDP, payments, healthcare)
- Hindi/English bilingual support
- Extracts: features, entities, user roles, integrations, constraints
- Produces structured requirements document for Saanvi

Tools:
- ask_user: Send a question to the user and wait for response
- save_requirement: Store an extracted requirement
- detect_compliance: Flag regulatory requirements
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

# Compliance keywords that trigger auto-detection
_COMPLIANCE_PATTERNS: dict[str, list[str]] = {
    "dpdp": ["personal data", "user data", "privacy", "consent", "aadhaar", "pan card"],
    "payments": ["payment", "razorpay", "upi", "billing", "subscription", "checkout"],
    "healthcare": ["patient", "medical", "health record", "prescription", "hipaa"],
    "ecommerce": ["product", "cart", "order", "inventory", "shipping", "catalog"],
}


class Tilotma(BaseAgent):
    """Project Manager — requirements gathering via structured conversation."""

    name = "tilotma"
    display_name = "Tilotma — Project Manager"
    default_complexity = TaskComplexity.MEDIUM

    def __init__(self) -> None:
        super().__init__()

        self.register_tool(ToolDefinition(
            name="save_requirement",
            description="Save an extracted requirement with category and priority.",
            parameters={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["feature", "entity", "user_role", "integration",
                                 "constraint", "non_functional", "compliance"],
                    },
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "priority": {"type": "string", "enum": ["must_have", "should_have", "nice_to_have"]},
                },
                "required": ["category", "title", "description", "priority"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="detect_compliance",
            description="Flag a detected compliance/regulatory requirement.",
            parameters={
                "type": "object",
                "properties": {
                    "regulation": {"type": "string"},
                    "reason": {"type": "string"},
                    "required_tables": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Database tables needed for compliance (e.g., audit_log, consent_records).",
                    },
                },
                "required": ["regulation", "reason"],
            },
        ))

    async def execute(
        self,
        pipeline_run_id: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Gather requirements from user input.

        In a live pipeline, this is a multi-turn conversation.
        For now, it processes the initial user description and extracts
        structured requirements.
        """
        # Get user's initial project description from context
        user_input = context.get("user_input", "")
        if not user_input:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error="No user input provided for requirements gathering",
            )

        # Build the requirements extraction prompt
        system_prompt = (
            "You are Tilotma, the Project Manager at NexSidi. Your job is to extract "
            "structured requirements from the user's project description.\n\n"
            "Extract and categorize ALL requirements:\n"
            "1. Features (what the system does)\n"
            "2. Entities (data objects: users, products, orders, etc.)\n"
            "3. User roles (admin, customer, vendor, etc.)\n"
            "4. Integrations (payment gateway, email, SMS, etc.)\n"
            "5. Constraints (tech stack preferences, budget, timeline)\n"
            "6. Non-functional (performance, security, scalability)\n"
            "7. Compliance (DPDP, payment regulations, industry-specific)\n\n"
            "Output a JSON object with keys: features, entities, user_roles, "
            "integrations, constraints, non_functional, compliance_flags.\n"
            "Each item should have: title, description, priority (must_have/should_have/nice_to_have)."
        )

        messages = [{"role": "user", "content": user_input}]

        try:
            response = await self.call_ai(
                messages=messages,
                system_prompt=system_prompt,
                task_type="general",
                temperature=0.3,  # Low temp for structured extraction
            )
        except Exception as exc:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error=f"AI call failed: {exc}",
            )

        # Auto-detect compliance needs from user input
        compliance_flags = self._detect_compliance(user_input)

        # Build output
        output = {
            "raw_input": user_input,
            "ai_analysis": response.content,
            "compliance_auto_detected": compliance_flags,
            "model_used": response.model_used,
            "tokens": {
                "input": response.input_tokens,
                "output": response.output_tokens,
            },
        }

        # Store in context engine
        await self.store_output(pipeline_run_id, output)

        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.COMPLETED,
            output=output,
            model_used=response.model_used,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )

    def _detect_compliance(self, text: str) -> list[dict[str, str]]:
        """Auto-detect compliance requirements from user text."""
        text_lower = text.lower()
        flags: list[dict[str, str]] = []

        for regulation, keywords in _COMPLIANCE_PATTERNS.items():
            matched = [kw for kw in keywords if kw in text_lower]
            if matched:
                flags.append({
                    "regulation": regulation,
                    "matched_keywords": ", ".join(matched),
                    "confidence": "high" if len(matched) >= 2 else "medium",
                })

        return flags


# Register the agent
_tilotma = Tilotma()
register_agent(_tilotma)
