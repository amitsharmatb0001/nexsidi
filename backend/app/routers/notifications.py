"""Notification API routes: list, read, mark-read.

All routes under /api/v1/notifications/. Tenant-scoped.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Query

from app.dependencies import CurrentContext
from app.schemas.project import NotificationListResponse, NotificationResponse
from app.services.notification import get_notification_service

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.get("/", response_model=NotificationListResponse)
async def list_notifications(
    ctx: CurrentContext,
    limit: int = Query(default=50, ge=1, le=200, description="Max notifications to return"),
    unread_only: bool = False,
) -> NotificationListResponse:
    """Get notifications for the current user."""
    # QUERY-FIX: limit validated by Query(ge=1, le=200) — OpenAPI docs show valid range
    svc = get_notification_service()
    # R8-FIX: Pass organization_id for tenant-scoped notification queries
    notifs = svc.get_user_notifications(
        ctx.user_id, limit=limit, unread_only=unread_only,
        organization_id=ctx.organization_id,
    )

    items = [
        NotificationResponse(
            title=n.title,
            body=n.body,
            link=n.link,
            channel=n.channel.value,
            created_at=n.created_at,
            read=n.is_read,
        )
        for n in notifs
    ]
    total = svc.notification_count(ctx.user_id, organization_id=ctx.organization_id)
    # R36-FIX: Use actual unread_count() instead of hardcoding unread=total.
    # Previously always returned unread=total because there was no read
    # tracking. Now uses NotificationPayload.is_read + unread_count().
    unread = svc.unread_count(ctx.user_id, organization_id=ctx.organization_id)

    return NotificationListResponse(
        notifications=items,
        total=total,
        unread=unread,
    )


@router.post("/read")
async def mark_as_read(
    ctx: CurrentContext,
    notification_ids: list[str] | None = None,
) -> dict:
    """R37-FIX: Mark notifications as read.

    If notification_ids is omitted, marks ALL as read.
    """
    svc = get_notification_service()
    count = svc.mark_as_read(
        ctx.user_id,
        notification_ids=notification_ids,
        organization_id=ctx.organization_id,
    )
    return {"marked_read": count}
