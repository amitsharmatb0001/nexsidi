"""WhatsApp Agent: receives project requests via WhatsApp Business API.

Behind ENABLE_WHATSAPP feature flag. Handles:
1. Incoming messages (text, image, audio, video, document)
2. Converts media attachments to requirements via input_processor
3. Sends progress updates during pipeline execution
4. Sends checkpoint approval requests
5. Delivers final project package link

WhatsApp Business API integration:
- Webhook receives incoming messages from Meta Cloud API
- Outbound messages sent via Meta Graph API
- Phone number verification + session management
- Message templates for structured notifications

Security:
- Webhook signature verification (HMAC-SHA256)
- Rate limiting per phone number
- No PII stored in logs (DPDP compliance)
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

from app.agents.base import (
    AgentResult,
    AgentStatus,
    ToolDefinition,
    register_agent,
    run_agent,
    store_output,
)
from app.services.ai_router import TaskComplexity

logger = structlog.get_logger(__name__)


class WhatsAppMessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"
    LOCATION = "location"
    INTERACTIVE = "interactive"


class WhatsAppSessionStatus(str, Enum):
    ACTIVE = "active"
    WAITING_INPUT = "waiting_input"
    PROCESSING = "processing"
    COMPLETED = "completed"
    EXPIRED = "expired"


@dataclass(slots=True)
class WhatsAppIncoming:
    """Parsed incoming WhatsApp message."""

    phone_number: str
    message_type: WhatsAppMessageType
    text: str = ""
    media_id: str = ""
    media_url: str = ""
    mime_type: str = ""
    caption: str = ""
    timestamp: str = ""
    phone_hash: str = ""  # R27-FIX-3: Hash of original phone, computed before redaction

    @classmethod
    def from_webhook(cls, payload: dict[str, Any]) -> WhatsAppIncoming | None:
        """Parse an incoming WhatsApp webhook payload (Meta Cloud API format)."""
        try:
            entry = payload.get("entry", [{}])[0]
            changes = entry.get("changes", [{}])[0]
            value = changes.get("value", {})
            messages = value.get("messages", [])
            if not messages:
                return None

            msg = messages[0]
            phone = msg.get("from", "")
            msg_type = msg.get("type", "text")
            text = ""
            media_id = ""
            caption = ""

            if msg_type == "text":
                text = msg.get("text", {}).get("body", "")
            elif msg_type in ("image", "audio", "video", "document"):
                media_data = msg.get(msg_type, {})
                media_id = media_data.get("id", "")
                caption = media_data.get("caption", "")

            # R27-FIX-3: Store the hash of the ORIGINAL phone for correlation,
            # not the redacted form. _redact_phone("919876543210") → "91******10"
            # which is useless for hashing (collides for all same-prefix numbers).
            phone_hash = _hash_phone(phone)
            return cls(
                phone_number=_redact_phone(phone),
                phone_hash=phone_hash,
                message_type=WhatsAppMessageType(msg_type) if msg_type in WhatsAppMessageType.__members__.values() else WhatsAppMessageType.TEXT,
                text=text,
                media_id=media_id,
                caption=caption,
                timestamp=msg.get("timestamp", ""),
            )
        except (IndexError, KeyError):
            return None


@dataclass(slots=True)
class WhatsAppOutgoing:
    """Outgoing WhatsApp message to send."""

    phone_number: str
    text: str = ""
    template_name: str = ""
    template_params: list[str] = field(default_factory=list)
    media_url: str = ""
    media_type: str = ""


# ── WhatsApp Message Templates ────────────────────────────────────

WHATSAPP_TEMPLATES: dict[str, str] = {
    "project_started": "Your project '{project_name}' is being built! We'll keep you updated on progress.",
    "checkpoint_approval": "Your project '{project_name}' needs your approval at {checkpoint_name}. Reply YES to approve or NO to reject.",
    "pipeline_progress": "Update on '{project_name}': Stage {stage} is {status}.",
    "deploy_complete": "Your project '{project_name}' is live! Access it at: {url}",
    "delivery_ready": "Your project '{project_name}' is ready for download! We'll send you the link shortly.",
    "error_notification": "There was an issue with '{project_name}': {error_summary}. Our team is looking into it.",
}


# ── WhatsApp Agent ────────────────────────────────────────────────


class WhatsAppAgent:
    """WhatsApp Business API integration agent.

    Behind ENABLE_WHATSAPP feature flag.
    """

    name = "whatsapp_agent"
    display_name = "WhatsApp Agent"
    default_complexity = TaskComplexity.MEDIUM
    default_model: str | None = None

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

        self.register_tool(ToolDefinition(
            name="send_message",
            description="Send a WhatsApp message to a user.",
            parameters={
                "type": "object",
                "properties": {
                    "phone": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["phone", "text"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="send_template",
            description="Send a templated WhatsApp message.",
            parameters={
                "type": "object",
                "properties": {
                    "phone": {"type": "string"},
                    "template": {"type": "string"},
                },
                "required": ["phone", "template"],
            },
        ))


    def register_tool(self, tool: "ToolDefinition") -> None:
        """Register a tool available to this agent."""
        self._tools[tool.name] = tool

    @property
    def tools(self) -> list["ToolDefinition"]:
        """All registered tools."""
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
        """Process WhatsApp-originated project requests."""
        whatsapp_input = context.get("whatsapp_input")
        if not whatsapp_input:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.COMPLETED,
                output={"status": "no_input", "message": "No WhatsApp input in context"},
            )

        incoming = WhatsAppIncoming.from_webhook(whatsapp_input)
        if incoming is None:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error="Failed to parse WhatsApp webhook payload",
            )

        # Route based on message type
        if incoming.message_type == WhatsAppMessageType.TEXT:
            requirements = incoming.text
        elif incoming.message_type in (WhatsAppMessageType.IMAGE, WhatsAppMessageType.DOCUMENT):
            requirements = f"[{incoming.message_type.value} attachment: {incoming.caption or 'no caption'}]"
        elif incoming.message_type == WhatsAppMessageType.AUDIO:
            requirements = "[audio message — requires voice-to-text processing]"
        else:
            requirements = incoming.text or incoming.caption or ""

        output = {
            "source": "whatsapp",
            "message_type": incoming.message_type.value,
            "requirements": requirements,
            # R27-FIX-3: Use pre-computed hash from original phone, not redacted form
            "phone_hash": incoming.phone_hash,
            "has_media": bool(incoming.media_id),
        }

        await store_output(self, pipeline_run_id, output)

        logger.info(
            "whatsapp_processed",
            message_type=incoming.message_type.value,
            has_media=bool(incoming.media_id),
        )

        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.COMPLETED,
            output=output,
        )

    async def send_progress(
        self,
        phone_hash: str,
        template_name: str,
        **params: str,
    ) -> WhatsAppOutgoing:
        """Send a progress update via WhatsApp template."""
        template = WHATSAPP_TEMPLATES.get(template_name, "")
        # R28-FIX-18: Use manual replacement instead of str.format() to prevent
        # format string injection. A project_name like "{__class__}" would trigger
        # attribute access via Python's format() mini-language.
        if template:
            text = template
            for key, value in params.items():
                text = text.replace(f"{{{key}}}", str(value))
        else:
            text = f"Update: {template_name}"

        msg = WhatsAppOutgoing(
            phone_number=phone_hash,
            text=text,
            template_name=template_name,
        )

        logger.info(
            "whatsapp_outgoing",
            template=template_name,
        )

        return msg


def verify_webhook_signature(
    payload_bytes: bytes,
    signature: str,
    app_secret: str,
) -> bool:
    """Verify WhatsApp webhook HMAC-SHA256 signature.

    Args:
        payload_bytes: Raw request body.
        signature: X-Hub-Signature-256 header value (sha256=...).
        app_secret: WhatsApp app secret.

    Returns:
        True if signature is valid.
    """
    if not signature.startswith("sha256="):
        return False

    expected = hmac.new(
        app_secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature[7:])


def _redact_phone(phone: str) -> str:
    """Redact phone number for logging (DPDP compliance)."""
    if len(phone) >= 4:
        return phone[:2] + "*" * (len(phone) - 4) + phone[-2:]
    return "****"


def _hash_phone(phone: str) -> str:
    """Hash phone number for storage (DPDP compliance)."""
    return hashlib.sha256(phone.encode("utf-8")).hexdigest()[:16]


# Register
_whatsapp_agent = WhatsAppAgent()
register_agent(_whatsapp_agent)
