"""Notification API routes: list, read, mark-read.

All routes under /api/v1/notifications/. Tenant-scoped.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter

from app.dependencies import CurrentContext
from app.schemas.project import NotificationListResponse, NotificationResponse
from app.services.notification import get_notification_service

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.get("/", response_model=NotificationListResponse)
async def list_notifications(
    ctx: CurrentContext,
    limit: int = 50,
    unread_only: bool = False,
) -> NotificationListResponse:
    """Get notifications for the current user."""
    svc = get_notification_service()
    notifs = svc.get_user_notifications(ctx.user_id, limit=limit, unread_only=unread_only)

    items = [
        NotificationResponse(
            title=n.title,
            body=n.body,
            link=n.link,
            channel=n.channel.value,
            created_at=n.created_at,
        )
        for n in notifs
    ]
    total = svc.notification_count(ctx.user_id)
    unread = total  # In production: count from DB where read=false

    return NotificationListResponse(
        notifications=items,
        total=total,
        unread=unread,
    )
