"""Webhook registration and management endpoints.

WEBHOOK-FIX: Allows organizations to register URLs to receive real-time
pipeline event notifications via HTTP POST with HMAC-SHA256 signing.
"""
from __future__ import annotations
import re
import structlog
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from app.dependencies import AdminContext, TenantSession
from app.services.webhook_service import WEBHOOK_EVENTS

router = APIRouter()
logger = structlog.get_logger(__name__)
_URL_RE = re.compile(r"^https://[a-zA-Z0-9._:/-]+$")


class WebhookCreate(BaseModel):
    url: str = Field(..., max_length=2048)
    secret: str | None = Field(None, max_length=128)
    events: list[str] = Field(default_factory=list)

    def validate_events(self) -> list[str]:
        invalid = [e for e in self.events if e not in WEBHOOK_EVENTS]
        if invalid:
            raise ValueError(f"Unknown events: {invalid}. Valid: {sorted(WEBHOOK_EVENTS)}")
        return self.events


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_webhook(body: WebhookCreate, ctx: AdminContext, session: TenantSession) -> dict:
    """Register a new webhook URL for pipeline event notifications."""
    if not _URL_RE.match(body.url):
        raise HTTPException(status_code=422, detail="URL must be HTTPS with valid characters only")
    events = body.validate_events() if body.events else list(WEBHOOK_EVENTS)
    row = await session.execute(
        text("INSERT INTO core.webhooks (organization_id, url, secret, events) VALUES (:oid::uuid, :url, :secret, :events) RETURNING id"),
        {"oid": str(ctx.organization_id), "url": body.url, "secret": body.secret, "events": events},
    )
    return {"id": str(row.fetchone()[0]), "url": body.url, "events": events, "message": "Webhook registered"}


@router.get("")
async def list_webhooks(ctx: AdminContext, session: TenantSession) -> dict:
    """List all registered webhooks for this organization."""
    rows = await session.execute(
        text("SELECT id, url, events, is_active, created_at, last_triggered_at, failure_count FROM core.webhooks WHERE organization_id = :oid::uuid ORDER BY created_at DESC"),
        {"oid": str(ctx.organization_id)},
    )
    hooks = [{"id": str(r.id), "url": r.url, "events": r.events, "is_active": r.is_active, "failure_count": r.failure_count} for r in rows.fetchall()]
    return {"webhooks": hooks, "total": len(hooks)}


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_webhook(webhook_id: str, ctx: AdminContext, session: TenantSession) -> Response:
    """Delete a registered webhook."""
    await session.execute(
        text("DELETE FROM core.webhooks WHERE id = :wid::uuid AND organization_id = :oid::uuid"),
        {"wid": webhook_id, "oid": str(ctx.organization_id)},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
