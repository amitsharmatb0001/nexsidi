"""Notification Service: sends notifications via web + email channels.

Phase 1 channels:
- Web (in-app): stored in notify.notifications table, delivered via WebSocket
- Email: queued via SMTP (async, non-blocking)

Phase 2 (behind feature flag):
- WhatsApp Business API

Design:
- All notifications flow through this service
- Channel selection is per-user preference (stored in auth.users)
- Templates are predefined per notification type
- Rate limiting: max 100 notifications per user per hour
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class NotificationChannel(str, Enum):
    WEB = "web"
    EMAIL = "email"


class NotificationType(str, Enum):
    """Predefined notification types with templates."""

    PIPELINE_STARTED = "pipeline.started"
    PIPELINE_COMPLETED = "pipeline.completed"
    PIPELINE_FAILED = "pipeline.failed"

    CHECKPOINT_READY = "checkpoint.ready"
    CHECKPOINT_APPROVED = "checkpoint.approved"
    CHECKPOINT_REJECTED = "checkpoint.rejected"

    DEPLOY_STARTED = "deploy.started"
    DEPLOY_COMPLETED = "deploy.completed"
    DEPLOY_FAILED = "deploy.failed"

    DELIVERY_READY = "delivery.ready"

    SECURITY_ALERT = "security.alert"

    TEAM_INVITE = "team.invite"
    TEAM_MEMBER_JOINED = "team.member_joined"

    SYSTEM_MAINTENANCE = "system.maintenance"


# Notification templates: type -> (title_template, body_template)
NOTIFICATION_TEMPLATES: dict[NotificationType, tuple[str, str]] = {
    NotificationType.PIPELINE_STARTED: (
        "Pipeline started: {project_name}",
        "Your project '{project_name}' has entered the build pipeline.",
    ),
    NotificationType.PIPELINE_COMPLETED: (
        "Pipeline complete: {project_name}",
        "Your project '{project_name}' has been built and is ready for review.",
    ),
    NotificationType.PIPELINE_FAILED: (
        "Pipeline failed: {project_name}",
        "Your project '{project_name}' build encountered errors. Check the pipeline dashboard for details.",
    ),
    NotificationType.CHECKPOINT_READY: (
        "Checkpoint ready: {project_name}",
        "Your project '{project_name}' is waiting for your approval at {checkpoint_name}.",
    ),
    NotificationType.CHECKPOINT_APPROVED: (
        "Checkpoint approved: {project_name}",
        "Checkpoint '{checkpoint_name}' has been approved. Pipeline is continuing.",
    ),
    NotificationType.CHECKPOINT_REJECTED: (
        "Checkpoint rejected: {project_name}",
        "Checkpoint '{checkpoint_name}' was rejected. Pipeline has been paused.",
    ),
    NotificationType.DEPLOY_STARTED: (
        "Deploying: {project_name}",
        "Your project '{project_name}' is being deployed to {provider}.",
    ),
    NotificationType.DEPLOY_COMPLETED: (
        "Deployed: {project_name}",
        "Your project '{project_name}' is live at {deployment_url}.",
    ),
    NotificationType.DEPLOY_FAILED: (
        "Deploy failed: {project_name}",
        "Deployment of '{project_name}' failed. Check deployment logs for details.",
    ),
    NotificationType.DELIVERY_READY: (
        "Project ready: {project_name}",
        "Your project '{project_name}' delivery package is ready for download.",
    ),
    NotificationType.SECURITY_ALERT: (
        "Security alert: {project_name}",
        "Security issues found in '{project_name}': {alert_summary}.",
    ),
    NotificationType.TEAM_INVITE: (
        "Team invitation",
        "You've been invited to join the team '{team_name}' at {organization_name}.",
    ),
    NotificationType.TEAM_MEMBER_JOINED: (
        "New team member",
        "{member_name} has joined the team '{team_name}'.",
    ),
    NotificationType.SYSTEM_MAINTENANCE: (
        "System maintenance",
        "NexSidi will undergo maintenance: {maintenance_details}.",
    ),
}


@dataclass(slots=True)
class NotificationPayload:
    """A notification to be sent."""

    notification_type: NotificationType
    user_id: str
    organization_id: str
    channel: NotificationChannel = NotificationChannel.WEB
    title: str = ""
    body: str = ""
    link: str = ""
    data: dict[str, Any] | None = None
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


class NotificationService:
    """Sends and manages notifications across channels.

    In production, web notifications are stored in DB + pushed via WebSocket.
    Email notifications are queued via SMTP.
    """

    def __init__(self) -> None:
        self._sent: list[NotificationPayload] = []  # In-memory for now
        self._rate_counts: dict[str, int] = {}       # user_id -> count this hour
        self._max_per_hour: int = 100

    async def send(
        self,
        notification_type: NotificationType,
        user_id: str,
        organization_id: str,
        channel: NotificationChannel = NotificationChannel.WEB,
        link: str = "",
        data: dict[str, Any] | None = None,
        **template_vars: str,
    ) -> NotificationPayload:
        """Send a notification.

        Args:
            notification_type: Which template to use.
            user_id: Target user UUID.
            organization_id: User's org UUID.
            channel: Delivery channel (web or email).
            link: Optional link to include.
            data: Optional extra data.
            **template_vars: Variables to fill in the template.

        Returns:
            The sent NotificationPayload.

        Raises:
            ValueError: If rate limit exceeded.
        """
        # Rate limiting
        if not self._check_rate_limit(user_id):
            raise ValueError(f"Rate limit exceeded for user {user_id}")

        # Render template
        title_tpl, body_tpl = NOTIFICATION_TEMPLATES.get(
            notification_type,
            ("{notification_type}", "Notification: {notification_type}"),
        )
        template_vars["notification_type"] = notification_type.value
        title = _safe_format(title_tpl, template_vars)
        body = _safe_format(body_tpl, template_vars)

        payload = NotificationPayload(
            notification_type=notification_type,
            user_id=user_id,
            organization_id=organization_id,
            channel=channel,
            title=title,
            body=body,
            link=link,
            data=data,
        )

        # Route to channel
        if channel == NotificationChannel.WEB:
            await self._send_web(payload)
        elif channel == NotificationChannel.EMAIL:
            await self._send_email(payload)

        self._sent.append(payload)
        self._increment_rate(user_id)

        logger.info(
            "notification_sent",
            notification_type=notification_type.value,
            channel=channel.value,
            user_id=user_id,
        )

        return payload

    async def send_to_project_users(
        self,
        notification_type: NotificationType,
        user_ids: list[str],
        organization_id: str,
        **template_vars: str,
    ) -> list[NotificationPayload]:
        """Send a notification to all users associated with a project."""
        results = []
        for uid in user_ids:
            try:
                payload = await self.send(
                    notification_type=notification_type,
                    user_id=uid,
                    organization_id=organization_id,
                    **template_vars,
                )
                results.append(payload)
            except ValueError:
                logger.warning("notification_rate_limited", user_id=uid)
        return results

    def get_user_notifications(
        self,
        user_id: str,
        limit: int = 50,
        unread_only: bool = False,
    ) -> list[NotificationPayload]:
        """Get notifications for a user (most recent first)."""
        user_notifs = [n for n in self._sent if n.user_id == user_id]
        user_notifs.sort(key=lambda n: n.created_at, reverse=True)
        return user_notifs[:limit]

    def notification_count(self, user_id: str) -> int:
        """Total notifications for a user."""
        return sum(1 for n in self._sent if n.user_id == user_id)

    # ── Channel Handlers ──────────────────────────────────────────

    async def _send_web(self, payload: NotificationPayload) -> None:
        """Store web notification (in production: DB + WebSocket push)."""
        logger.debug(
            "web_notification",
            title=payload.title,
            user_id=payload.user_id,
        )

    async def _send_email(self, payload: NotificationPayload) -> None:
        """Queue email notification (in production: SMTP via async worker)."""
        logger.debug(
            "email_notification_queued",
            title=payload.title,
            user_id=payload.user_id,
        )

    # ── Rate Limiting ─────────────────────────────────────────────

    def _check_rate_limit(self, user_id: str) -> bool:
        """Check if user is within rate limit (100/hour)."""
        return self._rate_counts.get(user_id, 0) < self._max_per_hour

    def _increment_rate(self, user_id: str) -> None:
        """Increment rate counter for user."""
        self._rate_counts[user_id] = self._rate_counts.get(user_id, 0) + 1


def _safe_format(template: str, vars: dict[str, str]) -> str:
    """Format a template string, leaving unknown vars as-is."""
    try:
        return template.format(**vars)
    except KeyError:
        # Fill what we can
        import re
        def replacer(m: re.Match) -> str:
            key = m.group(1)
            return vars.get(key, m.group(0))
        return re.sub(r"\{(\w+)\}", replacer, template)


# ── Singleton ───────────────────────────────────────────────────

_service: NotificationService | None = None


def get_notification_service() -> NotificationService:
    """Get or create the notification service singleton."""
    global _service
    if _service is None:
        _service = NotificationService()
    return _service
